# Unified Tag Management and Bulk Assignment

> **Date**: 2026-07-09
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Rework tag feature into unified management popover (add/delete/color) and multi-workspace tag assignment via selection
> **Estimated effort**: ~1-2 days

---

## Intent

### Problem statement & desired outcomes

Tag management in PowerAtlas is currently split across two disconnected UIs: (1) a per-workspace modal for assigning tags and setting workspace color, and (2) a minimal tag-colors popover that only edits colors. There is no way to create a tag ahead of assignment, delete a tag globally, or assign tags to multiple workspaces at once. The implicit orphan-pruning behavior silently removes tags that aren't assigned to any workspace, making standalone tag creation impossible.

The desired outcome is a unified tag management experience: one popover where users can create, delete, and color tags; and a multi-workspace settings modal that leverages existing multi-select to assign tags and colors to multiple workspaces in a single action.

### Success criteria

- SC-1: The existing tag-colors popover is extended into a unified tag management popover supporting: add new tag, delete tag (with confirmation showing affected workspace count), and set tag color (existing swatch UX preserved)
- SC-2: The "hidden" system tag cannot be deleted from the management popover
- SC-3: Clicking the gear icon on a workspace card when multiple cards are selected opens a joint settings modal showing "Settings — N workspaces" title
- SC-4: Joint modal displays mixed tag state: solid chips for tags on all selected workspaces, dimmed/indeterminate chips for tags on some, autocomplete for adding new tags
- SC-5: Joint modal includes compact info messages explaining what the user sees and what changes will do ("Tags shown as filled are on all selected workspaces. Dimmed tags are only on some." / "Changes apply to all selected workspaces.")
- SC-6: Adding/removing tags in the joint modal applies to all selected workspaces; color changes apply to all
- SC-7: Bulk tag assignment handles the 10-tag-per-workspace limit gracefully with partial success feedback (e.g. "Tag added to 2/3 workspaces. 1 workspace already has 10 tags.")
- SC-8: Orphan pruning logic removed from config load — tags persist in tag_settings until explicitly deleted via the management popover
- SC-9: A new API endpoint for tag deletion removes the tag from tag_settings AND unassigns it from all workspaces that have it
- SC-10: Existing single-workspace gear icon behavior preserved when no multi-selection is active

### Scope boundaries & non-goals

**In scope:**
- Extending the tag-colors popover with add/delete actions
- Multi-workspace settings modal triggered by gear icon during multi-select
- Mixed-state tag chip display (full/partial/absent)
- Bulk tag assignment/removal API endpoint
- Removing orphan tag pruning from config.py
- Tag deletion API endpoint (removes from tag_settings + all workspace assignments)
- Info messages in the joint modal

**Non-goals:**
- Tag rename (would require updating all workspace assignments — separate feature)
- Changes to session-level data model (sessions inherit workspace tags)
- Changes to the filter/dropdown behavior
- Changes to the provider filter or action bar launch functionality
- Drag-and-drop tag reordering
- Tag hierarchy or nested tags
- Changes to the single-workspace modal UX (beyond opening joint modal when multi-selected)

---

## Discovery

### Existing patterns & constraints

- Tag management split across `toggleTagColorsPopover()` (index.html:163) for colors and `openWorkspaceSettings()` (index.html:208) for assignment
- Multi-select infrastructure exists: `handleItemClick()` (index.html:133), `getSelectedWorkspaceCwds()` (index.html:184), action bar (index.html:77-80)
- Config model: `workspace_settings: dict[str, dict]` and `tag_settings: dict[str, dict]` (config.py:62-63)
- Orphan pruning at config.py:252-259 removes unassigned tags (except "hidden") on every `load_config()`
- Validation: tag names ≤64 chars, max 10 tags per workspace, colors ≤20 chars (web.py:1444-1453)
- "hidden" tag always preserved in tag_settings (config.py:250) and has special filter semantics (web.py:399-401)
- Color resolution waterfall: workspace color > first tag color > provider gradient (web.py:60-70)
- Atomic config save with lock (config.py:283-295); each API handler loads fresh config
- Custom htmx-mini requires `htmx.process(el)` after innerHTML swaps (project memory)
- Color palette hardcoded in 3 places: JS `_tagColorPalette`, workspace_settings_modal.html, launcher_modal.html

### Risks & mitigations

- **Orphan pruning removal may accumulate unused tags** — mitigated by explicit delete action in the unified popover; user has full control
- **Multi-workspace modal state complexity** — mixed tag state (full/partial/absent) requires careful JS state management; mitigated by keeping the existing chip UX pattern and adding visual differentiation only
- **Bulk assignment hitting 10-tag limit** — mitigated by partial-success model with clear feedback toast
- **Color palette duplication across 3 locations** — existing tech debt, not introduced by this change; could be consolidated but is non-goal

### Resolved decisions

- Q1: Where does the unified management section live? — A: Extend the existing tag-colors popover (palette button entry point). — Decision: Popover extended with add/delete alongside existing color swatches
- Q2: What triggers the joint popup for multi-workspace assignment? — A: The gear icon on any selected card opens joint modal for all selected workspaces. — Decision: Gear icon context-aware (single-workspace when nothing selected, joint when multi-selected); modal shows "N selected workspaces"
- Q3: How to display mixed tag state in joint modal? — A: Solid chips for all-selected, dimmed for some-selected, plus info messages. — Decision: Three-state tag display (full/partial/absent) with compact explanatory text
- Q4: What does "delete tag" do? — A: Remove from tag_settings + unassign from all workspaces, with confirmation showing affected count. "hidden" tag protected. — Decision: Global delete with confirmation; "hidden" undeletable
- Q5: How much info in the joint modal? — A: Compact — title with count, two short hint lines about mixed state and bulk application. — Decision: Minimal info UX (title + 2 hint lines)
- Q6: How to handle 10-tag limit in bulk assignment? — A: Partial success with feedback showing how many succeeded/skipped. — Decision: Partial success model with toast feedback
- Q7: How to handle standalone tag creation given orphan pruning? — A: Remove orphan pruning entirely; tags persist until explicit delete. — Decision: Remove pruning, rely on explicit delete via management popover

### Open items

None.

### Recommended approach

1. **Backend**: Remove orphan pruning from `load_config()`. Add `POST /api/tag/delete` endpoint (removes from tag_settings + all workspace assignments, protects "hidden"). Add `POST /api/workspace-settings/save-bulk` endpoint accepting `{cwds: [...], tags_add: [...], tags_remove: [...], color: "..."}` with partial-success response.
2. **Frontend — popover**: Extend `toggleTagColorsPopover()` to include an "Add tag" input at top/bottom and a delete button (✕) per tag row. Delete triggers confirmation via a small inline prompt or browser confirm().
3. **Frontend — joint modal**: Modify `openWorkspaceSettings()` to detect multi-selection (`getSelectedWorkspaceCwds().length > 1`). If multi-selected: fetch settings for all selected workspaces, compute tag intersection/partial sets, render with three-state chips, show info lines. On save, POST to the bulk endpoint.
4. **Tests**: Add tests for tag deletion endpoint, bulk save endpoint (including partial success), and removal of orphan pruning behavior.

---

## 1) Current State

**Config model** (`config.py:62-63`): `workspace_settings: dict[str, dict]` keyed by raw path, values `{"tags": list[str], "color": str}`. `tag_settings: dict[str, dict]` keyed by tag name, values `{"color": str}`.

**Orphan pruning** (`config.py:251-258`): On every `load_config()`, tags in `tag_settings` not assigned to any workspace (except `"hidden"`) are deleted. This makes standalone tag creation impossible.

**Tag color popover** (`index.html:163`, `toggleTagColorsPopover()`): Builds a floating div listing all tags from `window._tagColorData` (fetched from `GET /api/tags`). Each row shows tag name + mini color swatches. Clicking a swatch POSTs to `/api/tag/save`. No add/delete capability.

**Workspace settings modal** (`workspace_settings_modal.html`, opened by `openWorkspaceSettings()` at `index.html:208`): A `<dialog>` with tag chips input (add via typing + autocomplete, remove via ✕), color swatch picker, and save button. Always operates on a single workspace (`wsSettingsCwd` hidden input).

**Multi-select** (`index.html:133-134`): `handleItemClick()` toggles `.selected` class on `.workspace-card` elements, with shift-range support. `getSelectedWorkspaceCwds()` deduplicates and returns selected workspace paths. Currently used only for batch-launching.

**API endpoints**:
- `GET /api/tags` (`web.py:762`): Returns `[{name, color, count}]` from workspace_settings + tag_settings union
- `POST /api/tag/save` (`web.py:779`): Saves single tag color to `tag_settings`
- `GET /api/workspace-settings` (`web.py:1421`): Returns settings for one cwd + `all_tags` for autocomplete
- `POST /api/workspace-settings/save` (`web.py:1435`): Saves tags+color for one workspace

## 2) Goal

Unify tag management into a single extended popover (add, delete, color) and make the workspace settings modal context-aware (single vs. multi-workspace) with three-state tag display and bulk save.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Tag management entry point | Extend existing popover (palette button) | Dedicated modal, new panel | User said current popup is "perfect, just needs slight extension" |
| Multi-workspace trigger | Gear icon on any selected card | New action bar button, dedicated bulk-tag button | Reuses existing UX, enables color changes too |
| Mixed tag state display | Solid (all), dimmed (some), absent | Start blank (add-only), per-tag counts | Three-state is informative without clutter |
| Orphan pruning strategy | Remove entirely | Flag-based exemption, time-delayed prune | Simplest; explicit delete replaces implicit cleanup |
| Tag limit in bulk | Partial success with feedback | Fail entire batch, silent cap | Informative without blocking the operation |
| "hidden" tag protection | Undeletable in management popover | Soft warning only, no protection | Hidden has special system semantics that must be preserved |

## 4) External Dependencies & Costs

### Required external changes

None — this is a code-only change to an existing desktop application with no external infrastructure.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Backend — remove orphan pruning + add tag delete endpoint [QA]

**Goal**: Remove orphan tag pruning from config load and add a `POST /api/tag/delete` endpoint that globally removes a tag.

**File scope**: `src/power_atlas/config.py`, `src/power_atlas/web.py`, `tests/test_config.py`, `tests/test_web.py`

**Covers**: SC-8, SC-9, SC-2

**Changes**:

1. **Remove orphan pruning** (`config.py:251-258`): Delete the block that collects `assigned_tags` and filters `tag_settings`. Keep the `"hidden"` tag `setdefault` line (line 250).

```python
# REMOVE these lines (config.py:252-258):
#        assigned_tags: set[str] = set()
#        for ws in config.workspace_settings.values():
#            assigned_tags.update(ws.get("tags", []))
#        config.tag_settings = {
#            k: v for k, v in config.tag_settings.items()
#            if k == "hidden" or k in assigned_tags
#        }
```

2. **Add `POST /api/tag/delete`** (`web.py`, after `save_tag_settings` ~line 797):

```python
@app.post("/api/tag/delete", response_class=HTMLResponse)
async def delete_tag(request: Request):
    """Delete a tag globally: remove from tag_settings and all workspace assignments."""
    body = await request.json()
    tag_name = body.get("tag", "")
    if not tag_name:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Missing tag name", "level": "error"})
    if tag_name == "hidden":
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Cannot delete the 'hidden' system tag", "level": "error"})
    config = load_config()
    # Remove from tag_settings
    config.tag_settings.pop(tag_name, None)
    # Remove from all workspace assignments
    removed_count = 0
    for ws in config.workspace_settings.values():
        tags = ws.get("tags", [])
        if tag_name in tags:
            ws["tags"] = [t for t in tags if t != tag_name]
            removed_count += 1
    save_config(config)
    msg = f"Tag '{tag_name}' deleted"
    if removed_count:
        msg += f" (removed from {removed_count} workspace{'s' if removed_count != 1 else ''})"
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": msg, "level": "success"})
```

3. **Tests**: Add `test_tag_delete_removes_globally`, `test_tag_delete_hidden_protected`, `test_tag_delete_nonexistent_succeeds`, `test_orphan_pruning_removed`.

**Exit criteria**:
- [x] Orphan pruning block removed from `load_config()`
- [x] Tags persist in `tag_settings` after config reload even when not assigned to any workspace
- [x] `POST /api/tag/delete` removes tag from `tag_settings` + all workspace assignments
- [x] Deleting "hidden" returns error toast
- [x] Tests pass for new endpoint and pruning removal

#### Implementation (2026-07-09, code: 159f571)

Removed the orphan tag pruning block from `load_config()` in config.py — tags now persist in `tag_settings` regardless of workspace assignments. Added a `POST /api/tag/delete` endpoint to web.py that globally removes a tag from `tag_settings` and all workspace assignments using list comprehension (per review finding #9), with protection against deleting the "hidden" system tag. Updated `test_tag_settings_round_trip` to no longer require workspace assignments for tag persistence, added `test_orphan_pruning_removed` in test_config.py, and added three new tests in test_web.py covering global deletion, hidden-tag protection, and nonexistent-tag graceful handling.

Review auto-fix (391fe53): Added input validation (type check, length, control chars) consistent with sibling endpoints, switched loop to `.values()`, added early-return when tag doesn't exist anywhere (avoids unnecessary disk write).

### Phase 2: Frontend — unified tag management popover [QA]

**Goal**: Extend the existing tag-colors popover to support adding new tags and deleting existing tags alongside color editing.

**File scope**: `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`

**Covers**: SC-1, SC-2

**Changes**:

1. **Extend `toggleTagColorsPopover()`** (`index.html:163`): Add an "Add tag" input field at the bottom of the popover, and a delete button (✕) per tag row. The "hidden" tag row gets no delete button.

```javascript
// In toggleTagColorsPopover(), after building tag rows:
// Add delete button per row (except hidden)
tags.forEach(function(t) {
  var eName = _escHtml(t.name);
  h += '<div class="tag-color-row" data-tag="' + eName + '">';
  h += '<span class="tag-color-name">' + eName + '</span>';
  h += '<div class="tag-color-swatches">';
  // ... existing swatch buttons ...
  h += '</div>';
  if (t.name !== 'hidden') {
    h += '<button type="button" class="tag-delete-btn" data-tag="' + eName + '" title="Delete tag">&times;</button>';
  }
  h += '</div>';
});
// Add "new tag" input at bottom
h += '<div class="tag-add-row">';
h += '<input type="text" class="tag-add-input" id="tagAddInput" placeholder="New tag..." maxlength="64">';
h += '<button type="button" class="tag-add-btn" id="tagAddBtn">+</button>';
h += '</div>';
```

2. **Delete handler**: On delete button click, show `confirm()` with affected workspace count (from `_tagColorData`'s `count` field), then POST to `/api/tag/delete`.

```javascript
pop.addEventListener('click', function(e) {
  var delBtn = e.target.closest('.tag-delete-btn');
  if (delBtn) {
    var tagName = delBtn.dataset.tag;
    var tagData = (window._tagColorData || []).find(function(t) { return t.name === tagName; });
    var count = tagData ? tagData.count : 0;
    var msg = 'Delete tag "' + tagName + '"?';
    if (count > 0) msg += '\nThis will remove it from ' + count + ' workspace(s).';
    if (!confirm(msg)) return;
    fetch('/api/tag/delete', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({tag: tagName})})
      .then(function(r) { return r.text(); })
      .then(function(t) { showToast(t); initWorkspaceFilters(); refreshCards(false); pop.remove(); });
    return;
  }
  // ... existing swatch click handler ...
});
```

3. **Add handler**: On add button click (or Enter in input), POST to `/api/tag/save` with empty color to create the tag, then refresh.

```javascript
var addBtn = pop.querySelector('#tagAddBtn');
var addInput = pop.querySelector('#tagAddInput');
function doAddTag() {
  var name = addInput.value.trim();
  if (!name || name.length > 64) return;
  fetch('/api/tag/save', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tag: name, color: ''})})
    .then(function(r) { return r.text(); })
    .then(function(t) { showToast(t); initWorkspaceFilters(); pop.remove(); toggleTagColorsPopover(); });
}
addBtn.onclick = doAddTag;
addInput.onkeydown = function(e) { if (e.key === 'Enter') { e.preventDefault(); doAddTag(); } };
```

4. **CSS** (`style.css`): Add styles for `.tag-delete-btn` and `.tag-add-row`.

```css
.tag-delete-btn { background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 14px; padding: 2px 4px; border-radius: 3px; transition: color 0.15s, background 0.15s; }
.tag-delete-btn:hover { color: #ef4444; background: rgba(239,68,68,0.1); }
.tag-add-row { display: flex; gap: 6px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
.tag-add-input { flex: 1; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 4px 8px; font-size: 12px; color: var(--text); }
.tag-add-btn { background: var(--accent); color: white; border: none; border-radius: var(--radius-sm); padding: 4px 10px; cursor: pointer; font-size: 14px; font-weight: 600; }
.tag-add-btn:hover { opacity: 0.85; }
```

**Exit criteria**:
- [ ] Tag management popover shows add input + add button at bottom
- [ ] Each tag row (except "hidden") shows a delete button
- [ ] Delete shows confirmation with workspace count, removes tag globally on confirm
- [ ] Add creates a new tag in `tag_settings` (persists — no orphan pruning)
- [ ] Popover refreshes after add/delete operations

### Phase 3: Backend — bulk workspace settings endpoint [QA]

**Goal**: Add a bulk save endpoint that applies tag additions/removals and color changes to multiple workspaces, with partial-success semantics.

**File scope**: `src/power_atlas/web.py`, `tests/test_web.py`

**Covers**: SC-6, SC-7

**Changes**:

1. **Add `POST /api/workspace-settings/save-bulk`** (`web.py`, after `save_workspace_settings_api`):

```python
@app.post("/api/workspace-settings/save-bulk", response_class=HTMLResponse)
async def save_workspace_settings_bulk(request: Request):
    """Bulk-apply tag additions/removals and color to multiple workspaces."""
    body = await request.json()
    cwds = body.get("cwds", [])
    tags_add = body.get("tags_add", [])
    tags_remove = body.get("tags_remove", [])
    color = body.get("color")  # None = don't change, "" = reset, "..." = set

    # Validation
    if not isinstance(cwds, list) or not cwds:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "No workspaces selected", "level": "error"})
    if len(cwds) > 50:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Max 50 workspaces per batch", "level": "error"})
    for t in tags_add + tags_remove:
        if not isinstance(t, str) or not t or len(t) > 64 or any(ord(ch) < 0x20 for ch in t):
            return templates.TemplateResponse(request, "partials/toast.html", {
                "message": f"Invalid tag name: {t!r}", "level": "error"})
    if set(tags_add) & set(tags_remove):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Cannot add and remove the same tag", "level": "error"})
    if color is not None and (len(color) > 20 or any(ord(ch) < 0x20 for ch in color)):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Invalid color value", "level": "error"})

    config = load_config()
    from .data import _normalize_path
    skipped = 0
    modified = 0

    for cwd in cwds:
        if not cwd or len(cwd) > 512:
            continue
        # Find or create workspace entry
        norm = _normalize_path(cwd)
        existing_key = next((k for k in config.workspace_settings if _normalize_path(k) == norm), None)
        key = existing_key or cwd
        ws = config.workspace_settings.setdefault(key, {"tags": [], "color": ""})
        ws.setdefault("tags", [])
        ws.setdefault("color", "")

        changed = False
        # Remove tags
        before_len = len(ws["tags"])
        ws["tags"] = [t for t in ws["tags"] if t not in tags_remove]
        if len(ws["tags"]) != before_len:
            changed = True
        # Add tags (respect 10-tag limit)
        ws_skipped = False
        for t in tags_add:
            if t in ws["tags"]:
                continue
            if len(ws["tags"]) >= 10:
                ws_skipped = True
                continue
            ws["tags"].append(t)
            changed = True
        if ws_skipped:
            skipped += 1
        # Color
        if color is not None and ws["color"] != color:
            ws["color"] = color
            changed = True
        if changed:
            modified += 1

    # Ensure added tags exist in tag_settings
    for t in tags_add:
        config.tag_settings.setdefault(t, {"color": ""})

    save_config(config)
    msg = f"Updated {modified} workspace{'s' if modified != 1 else ''}"
    if skipped:
        msg += f" ({skipped} workspace{'s' if skipped != 1 else ''} hit 10-tag limit)"
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": msg, "level": "success" if not skipped else "warning"})
```

2. **Add `POST /api/workspace-settings-bulk`** (returns settings for multiple cwds):

```python
@app.post("/api/workspace-settings-bulk")
async def get_workspace_settings_bulk(request: Request):
    """Return workspace settings for multiple cwds."""
    body = await request.json()
    cwds = body.get("cwds", [])
    if not isinstance(cwds, list) or not cwds or len(cwds) > 50:
        raise HTTPException(status_code=400, detail="cwds must be a list of 1-50 paths")
    config = load_config()
    from .config import get_workspace_settings
    results = {}
    for cwd in cwds:
        if isinstance(cwd, str) and cwd:
            results[cwd] = get_workspace_settings(config, cwd.strip())
    # All known tags for autocomplete
    all_tags = set()
    for ws in config.workspace_settings.values():
        all_tags.update(ws.get("tags", []))
    all_tags.update(config.tag_settings.keys())
    return {"workspaces": results, "all_tags": sorted(all_tags)}
```

3. **Tests**: `test_bulk_save_adds_tags_to_multiple`, `test_bulk_save_partial_success_10_tag_limit`, `test_bulk_save_removes_tags`, `test_bulk_save_color_applies_to_all`, `test_bulk_get_returns_multiple`.

**Exit criteria**:
- [ ] `POST /api/workspace-settings/save-bulk` adds/removes tags and sets color across multiple workspaces
- [ ] Partial success returns warning-level toast with skip count
- [ ] `GET /api/workspace-settings-bulk` returns settings for multiple cwds
- [ ] Added tags auto-created in `tag_settings` if not present
- [ ] Tests pass for bulk endpoints

### Phase 4: Frontend — multi-workspace settings modal [QA]

**Goal**: Make the workspace settings modal context-aware: detect multi-selection, fetch bulk settings, show mixed-state tags with info messages, and save via the bulk endpoint.

**File scope**: `src/power_atlas/templates/index.html`, `src/power_atlas/templates/partials/workspace_settings_modal.html`, `src/power_atlas/static/style.css`, `README.md`

**Covers**: SC-3, SC-4, SC-5, SC-6, SC-10

**Changes**:

1. **Modify `openWorkspaceSettings()`** (`index.html:208`): Detect if other workspace cards are selected. If multi-selected, fetch bulk settings and open in multi-mode.

```javascript
function openWorkspaceSettings(btn) {
  var card = btn.closest('.workspace-card');
  var selectedCwds = getSelectedWorkspaceCwds();
  // If the clicked card is selected AND there are multiple selections, use bulk mode
  var isBulk = card.classList.contains('selected') && selectedCwds.length > 1;
  if (isBulk) {
    openBulkWorkspaceSettings(selectedCwds);
  } else {
    openSingleWorkspaceSettings(card.dataset.cwd);
  }
}
```

2. **Add `openSingleWorkspaceSettings(cwd)`**: Extract existing single-workspace logic into this function (same behavior as current `openWorkspaceSettings`).

3. **Add `openBulkWorkspaceSettings(cwds)`**: New function for multi-workspace mode.

```javascript
function openBulkWorkspaceSettings(cwds) {
  fetch('/api/workspace-settings-bulk', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cwds: cwds})})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var m = document.getElementById('workspaceSettingsModal');
      document.getElementById('wsSettingsCwd').value = JSON.stringify(cwds);
      _wsAllTags = d.all_tags || [];
      _wsBulkMode = true;
      _wsBulkCwds = cwds;

      // Compute tag states: all, some, none
      var tagCounts = {};
      var colors = new Set();
      Object.values(d.workspaces).forEach(function(ws) {
        (ws.tags || []).forEach(function(t) {
          tagCounts[t] = (tagCounts[t] || 0) + 1;
        });
        colors.add(ws.color || '');
      });
      var total = cwds.length;
      _wsTagsFull = []; _wsTagsPartial = [];
      Object.keys(tagCounts).forEach(function(t) {
        if (tagCounts[t] === total) _wsTagsFull.push(t);
        else _wsTagsPartial.push(t);
      });
      _wsTags = _wsTagsFull.slice();  // editable: starts with "all" tags
      _wsTagsToAdd = [];
      _wsTagsToRemove = [];

      // Title and info
      document.getElementById('wsSettingsTitle').textContent = 'Settings \u2014 ' + total + ' workspaces';
      renderBulkWsTags();

      // Color: if all same, select it; otherwise no selection
      var commonColor = colors.size === 1 ? [...colors][0] : null;
      document.getElementById('wsSettingsColor').value = commonColor || '';
      document.querySelectorAll('#wsColorPicker .color-swatch').forEach(function(s) {
        s.classList.toggle('selected', commonColor !== null && s.dataset.color === commonColor);
      });
      m.showModal();
    });
}
```

4. **Add `renderBulkWsTags()`**: Renders three-state chips + info messages.

```javascript
function renderBulkWsTags() {
  var chips = document.getElementById('wsTagsChips');
  var infoEl = document.getElementById('wsTagsInfo');
  if (!infoEl) {
    infoEl = document.createElement('div');
    infoEl.id = 'wsTagsInfo';
    infoEl.className = 'ws-tags-info';
    document.getElementById('wsTagsContainer').insertBefore(infoEl, chips);
  }
  infoEl.innerHTML = '<span class="ws-info-line">Filled tags = on all workspaces. Dimmed = on some only.</span>' +
    '<span class="ws-info-line">Changes apply to all ' + _wsBulkCwds.length + ' selected workspaces.</span>';
  infoEl.style.display = '';

  chips.innerHTML = '';
  // Full tags (solid)
  _wsTagsFull.filter(function(t) { return _wsTagsToRemove.indexOf(t) < 0; }).forEach(function(tag) {
    var chip = document.createElement('span');
    chip.className = 'ws-tag-chip ws-tag-full';
    chip.textContent = tag;
    var x = document.createElement('button');
    x.type = 'button'; x.className = 'ws-tag-remove'; x.textContent = '\u00d7';
    x.onclick = function() { _wsTagsToRemove.push(tag); renderBulkWsTags(); };
    chip.appendChild(x);
    chips.appendChild(chip);
  });
  // Partial tags (dimmed)
  _wsTagsPartial.filter(function(t) { return _wsTagsToRemove.indexOf(t) < 0; }).forEach(function(tag) {
    var chip = document.createElement('span');
    chip.className = 'ws-tag-chip ws-tag-partial';
    chip.textContent = tag;
    var x = document.createElement('button');
    x.type = 'button'; x.className = 'ws-tag-remove'; x.textContent = '\u00d7';
    x.onclick = function() { _wsTagsToRemove.push(tag); renderBulkWsTags(); };
    chip.appendChild(x);
    chips.appendChild(chip);
  });
  // Added tags (new, solid accent)
  _wsTagsToAdd.forEach(function(tag) {
    var chip = document.createElement('span');
    chip.className = 'ws-tag-chip ws-tag-new';
    chip.textContent = tag;
    var x = document.createElement('button');
    x.type = 'button'; x.className = 'ws-tag-remove'; x.textContent = '\u00d7';
    x.onclick = function() { _wsTagsToAdd.splice(_wsTagsToAdd.indexOf(tag), 1); renderBulkWsTags(); };
    chip.appendChild(x);
    chips.appendChild(chip);
  });
}
```

5. **Modify `saveWorkspaceSettings()`**: Branch on `_wsBulkMode`.

```javascript
function saveWorkspaceSettings(e) {
  e.preventDefault();
  if (_wsBulkMode) {
    var color = document.getElementById('wsSettingsColor').value;
    var payload = {cwds: _wsBulkCwds, tags_add: _wsTagsToAdd, tags_remove: _wsTagsToRemove};
    // Only include color if user changed it
    if (color !== '' || document.querySelector('#wsColorPicker .color-swatch.selected')) {
      payload.color = color;
    }
    fetch('/api/workspace-settings/save-bulk', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)})
      .then(function(r) { return r.text(); })
      .then(function(t) { showToast(t); document.getElementById('workspaceSettingsModal').close();
        _wsBulkMode = false; refreshCards(false); initWorkspaceFilters(); });
  } else {
    // existing single-workspace save logic
    var cwd = document.getElementById('wsSettingsCwd').value;
    var color = document.getElementById('wsSettingsColor').value;
    fetch('/api/workspace-settings/save', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cwd: cwd, tags: _wsTags, color: color})})
      .then(function(r) { return r.text(); })
      .then(function(t) { showToast(t); document.getElementById('workspaceSettingsModal').close();
        refreshCards(false); initWorkspaceFilters(); });
  }
}
```

6. **Modify `addWsTag()`**: In bulk mode, push to `_wsTagsToAdd` instead of `_wsTags`, and re-render with `renderBulkWsTags()`.

7. **CSS**: Add styles for `.ws-tag-partial`, `.ws-tag-new`, `.ws-tags-info`.

```css
.ws-tag-chip.ws-tag-partial { opacity: 0.5; border-style: dashed; }
.ws-tag-chip.ws-tag-new { background: var(--accent); color: white; }
.ws-tags-info { font-size: 11px; color: var(--text-muted); padding: 4px 0 6px; line-height: 1.5; }
.ws-info-line { display: block; }
```

8. **Modal reset on close**: Add close event listener to reset all bulk state:

```javascript
document.getElementById('workspaceSettingsModal').addEventListener('close', function() {
  _wsBulkMode = false;
  _wsTagsToAdd = [];
  _wsTagsToRemove = [];
  _wsBulkCwds = [];
  var infoEl = document.getElementById('wsTagsInfo');
  if (infoEl) infoEl.style.display = 'none';
});
```

9. **Gear icon detection logic**: The gear icon detection must handle the case where the clicked card may not itself be `.selected`. If other cards are selected (count > 0), include the clicked card in the bulk set regardless:

```javascript
function openWorkspaceSettings(btn) {
  var card = btn.closest('.workspace-card');
  var selectedCwds = getSelectedWorkspaceCwds();
  // Include clicked card's cwd if not already in selection
  var clickedCwd = card.dataset.cwd;
  if (selectedCwds.length > 0 && selectedCwds.indexOf(clickedCwd) < 0) {
    selectedCwds.push(clickedCwd);
  }
  // Bulk mode if 2+ workspaces involved
  if (selectedCwds.length > 1) {
    openBulkWorkspaceSettings(selectedCwds);
  } else {
    openSingleWorkspaceSettings(clickedCwd);
  }
}
```

10. **Color change tracking**: Add a `_wsColorChanged` flag to track whether user explicitly picked a color:

```javascript
var _wsColorChanged = false;
document.getElementById('wsColorPicker').addEventListener('click', function(e) {
  var btn = e.target.closest('.color-swatch');
  if (!btn) return;
  _wsColorChanged = true;
  // ... existing handler ...
});
// In saveWorkspaceSettings bulk branch:
if (_wsColorChanged) { payload.color = document.getElementById('wsSettingsColor').value; }
// Reset in close handler:
// _wsColorChanged = false;
```

11. **Accessibility**: Add `aria-label` attributes to delete buttons, add input, and partial-state chips. Add `title` on partial chips showing "Applied to N of M selected workspaces".

**Exit criteria**:
- [ ] Gear icon on a selected card (with multi-selection) opens joint modal with "Settings — N workspaces" title
- [ ] Gear icon on unselected card with other cards selected includes clicked card in bulk set
- [ ] Full tags shown as solid chips, partial tags as dimmed/dashed chips with `title` showing count
- [ ] Info messages displayed explaining the state and behavior
- [ ] Adding a tag in bulk mode queues it for the bulk save (no client-side 10-tag check)
- [ ] Removing a tag queues removal for all selected workspaces
- [ ] Save in bulk mode POSTs to `/api/workspace-settings/save-bulk` with `color` only when changed
- [ ] Single-workspace behavior unchanged when no multi-selection active
- [ ] Modal close event resets all bulk state (`_wsBulkMode`, `_wsTagsToAdd`, `_wsTagsToRemove`, `_wsColorChanged`)
- [ ] Accessibility: `aria-label` on delete buttons, partial chips, add input
- [ ] Error handling: `.catch()` on bulk fetch with error toast
- [ ] `README.md` updated with unified tag management and bulk assignment feature description

## 6) Risk Assessment
|---|---|---|
| Tags accumulate without pruning | Low — cosmetic clutter | Explicit delete in popover; user manages lifecycle |
| Bulk save performance with many workspaces | Low — 50 max cap, single config write | Cap enforced server-side; atomic save is fast |
| Mixed-state UX confusion | Medium — new concept for users | Info messages explain full/partial/new states |
| Gear icon dual behavior (single vs. bulk) | Low — potential user surprise | Title + info messages make mode obvious |
| Color palette inconsistency across 3 locations | Low — pre-existing debt | Out of scope; not worsened |
| Selection lost after refreshCards | Medium — bulk save clears workspace selection | After bulk save, skip full refreshCards or re-apply selection from saved cwds |
| TOCTOU race in load-modify-save | Low — pre-existing pattern | Serialized by threading lock; acceptable for desktop app |

## 7) Verification

- Run `pytest tests/test_config.py tests/test_web.py` — all pass
- Manual: open app, create a tag via popover → persists after reload (no pruning)
- Manual: delete a tag → removed from tag_settings + all workspace assignments
- Manual: select 2+ workspaces → click gear → joint modal with mixed state
- Manual: add/remove tags in bulk → partial success toast when limit hit
- Manual: single workspace gear (no selection) → existing behavior unchanged

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Update "Workspace tags" feature description to mention unified management and bulk assignment | 4 |

## 9) Implementation Divergences from Plan

<Reserved -- filled during implementation>

## Review Log

### 2026-07-09 — Plan Review (via /qplan, high effort)

4 personas (Architect, Senior engineer, End-user advocate, Reliability engineer). 14 unique findings after dedup. 10 auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | GET bulk endpoint uses query params — URL length exceeded for Windows paths | Resolved — changed to POST with JSON body |
| 2 | High | No modal close handler resets `_wsBulkMode` — stale state corrupts next open | Resolved — added close event listener resetting all bulk state |
| 3 | High | `[P:1]`/`[P:2]` parallel annotations incorrect — Phase 2 depends on Phase 1 | Resolved — removed parallel annotations (phases are sequential) |
| 4 | High | Gear icon discoverability — no visual cue that gear works differently in multi-select | Noted — implementation should add tooltip/badge change on gear during multi-select |
| 5 | Medium | Gear detection uses `card.classList.contains('selected')` — misses unselected clicked card | Resolved — detection now includes clicked card if others are selected |
| 6 | Medium | `skipped` counter counted per-tag not per-workspace — misleading message | Resolved — counts per-workspace, message says "N workspaces hit 10-tag limit" |
| 7 | Medium | TOCTOU race in load→modify→save pattern for bulk/delete endpoints | Noted — pre-existing pattern across all endpoints; documented as accepted risk |
| 8 | Medium | Color change detection missing — can't distinguish "didn't touch" from "picked no color" | Resolved — added `_wsColorChanged` flag tracked in swatch click handler |
| 9 | Medium | `tags.remove()` in delete only removes first occurrence; fragile with data corruption | Resolved — changed to list comprehension `[t for t in tags if t != tag_name]` |
| 10 | Medium | No validation for `tags_add`/`tags_remove` overlap in bulk save | Resolved — added intersection check returning error |
| 11 | Medium | No keyboard accessibility in popover (tabindex, Escape, arrow navigation) | Noted — added to Phase 4 exit criteria as accessibility item |
| 12 | Low | Stale tag filter after delete (active filter matches deleted tag) | Noted — implementation should reset active filter if deleted tag was selected |
| 13 | Low | Popover delete handler calls `pop.remove()` regardless of fetch success | Noted — implementation should check response before closing |
| 14 | Low | Add-tag in popover doesn't check for duplicate tag name client-side | Noted — implementation should check `_tagColorData` before posting |


### 2026-07-09 -- Implementation Review (after Phase 1, personas: Senior engineer, Reliability engineer, Security auditor, Maintainability reviewer)

Implementation health: Green.
4 findings (0 High, 0 Medium, 4 Low). Cycle 2 skipped — cycle 1 findings all Low + auto-fixes purely mechanical.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | `delete_tag` lacked length/control-char validation present in sibling endpoints | Fixed — added matching isinstance+length+control-char guard (391fe53) |
| 2 | Low | Unused loop variable `ws_path` in workspace iteration | Fixed — switched to `.values()` (391fe53) |
| 3 | Low | Nonexistent tag delete called `save_config` (disk write) when nothing changed | Fixed — added early-return when tag not found anywhere (391fe53) |
| 4 | Low | No type check on `body.get("tag")` — non-string could bypass empty check | Fixed — added `isinstance(tag_name, str)` in the combined validation guard (391fe53) |

QA verification: PASS (TestClient HTTP stack, 3 endpoint scenarios verified).