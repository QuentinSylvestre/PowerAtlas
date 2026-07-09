# Unified Tag Management and Bulk Assignment

> **Date**: 2026-07-09
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Rework tag feature into unified management popover (add/delete/color) and multi-workspace tag assignment via selection

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
